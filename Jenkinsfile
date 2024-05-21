pipeline {
  agent {
    node {
      label 'docker'
    }
  }
  stages {
  stage('Test') {
  parallel {
    stage('Test Python Baseline') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-baseline.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh baseline'
          dir("tempsrc") { deleteDir() }
        }
        junit 'baseline-results.xml'
      }
    }
    stage('Test Python Latest') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'python-latest.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh latest'
          dir("tempsrc") { deleteDir() }
        }
        junit 'latest-results.xml'
      }
    }
    stage('Test Ubuntu Baseline') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'ubuntu-baseline.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh ubuntu-baseline'
          dir("tempsrc") { deleteDir() }
        }
        junit 'ubuntu-baseline-results.xml'
      }
    }
    stage('Test Ubuntu Latest') {
      agent {
        dockerfile {
          dir 'src/tests'
          filename 'ubuntu-latest.Dockerfile'
        }
      }
      steps {
        ansiColor(colorMapName: 'xterm') {
          dir("tempsrc") { deleteDir() }
          dir("tempsrc") { sh 'ls -la' }
          // Avoid git chowning .git/index to root which will cause the next build to fail
          // Work around old docker version in jenkins that can't change cwd:
          sh 'cd tempsrc && ../src/tests/run_jenkins_tests.sh ubuntu-latest'
          dir("tempsrc") { deleteDir() }
        }
        junit 'ubuntu-latest-results.xml'
      }
    }
  }
  }
  }
  environment {
    PYTHONDONTWRITEBYTECODE = '1'
  }
  post {
    failure {
      mail(to: 'alr48@cl.cam.ac.uk', subject: "Failed Pipeline: ${currentBuild.fullDisplayName}", body: "Something is wrong with ${env.BUILD_URL}")
    }
  }
  options {
    checkoutToSubdirectory('src')
    timestamps()
  }
}
